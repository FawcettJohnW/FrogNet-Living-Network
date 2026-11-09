#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_header_page.py -- [RT_HEADER_MENU_V1] the header on a phone (Galaxy S21 width, 360 CSS px) and on a desktop:
#   1 phone, connected as a team lead (every header button shown): Chat in the header and in view, ☰ shown, nothing
#     cut off; the rest under ☰; ☰ > Diag opens Diagnostics; ☰ > Layers opens the layers
#   2 desktop (1400 px): every button in the header, no ☰
import json, os, sys
from playwright.sync_api import sync_playwright
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(HERE)
FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)
STUB = "window.SENT=[]; window.rtNative={send(l){SENT.push(JSON.parse(l))}, log(){}, teamMembers(){}, teamHost(){}, teamPlace(){}}"
def up(pg):
    pg.add_init_script(STUB); pg.goto("file://" + os.path.join(APP, "ui", "index.html"))
    dl = lambda o: pg.evaluate("l => rtDeliver(l)", json.dumps(o))
    pg.evaluate("rtInit({dev:'p1',name:'Pixel',door:'10.170.170.1',role:'team',team:'Charlie',platform:'android',layers:[],enrolled:{lead:true}})")
    pg.wait_for_selector("#pick:not([hidden])"); pg.click("[data-pick=team]"); pg.click("#connect button[type=submit], #connect .go")
    dl({"ev": "connected", "role": "team", "view": "team", "team": "Charlie", "name": "Pixel", "dev": "p1", "door": "10.170.170.1", "build": "t"})
    dl({"ev": "mission", "id": "standing", "name": "Standing operations", "bag": {"name": "Standing operations"}})
    pg.wait_for_timeout(400)
inview = "(id)=>{const b=document.getElementById(id), r=b.getBoundingClientRect(), h=document.querySelector('header').getBoundingClientRect(); return !b.hidden && b.closest('header') && r.right<=h.right+1 && r.width>0}"
with sync_playwright() as pw:
    br = pw.chromium.launch(); errs = []
    pg = br.new_page(viewport={"width": 360, "height": 780}, device_scale_factor=3); pg.on("pageerror", lambda e: errs.append(str(e)))
    up(pg)
    hdr = pg.evaluate("[...document.querySelectorAll('header button')].filter(b=>!b.hidden).map(b=>b.id)")
    menu = pg.evaluate("[...document.querySelectorAll('#menu button')].filter(b=>!b.hidden).map(b=>b.id)")
    fits = pg.evaluate("(()=>{const h=document.querySelector('header'); return h.scrollWidth<=h.clientWidth+1})()")
    chat, mb = pg.evaluate(inview, "chatBtn"), pg.evaluate(inview, "menuBtn")
    pg.click("#menuBtn"); pg.screenshot(path="/tmp/claude-0/header-phone.png"); pg.click("#menu #diagBtn")
    diag = pg.is_visible("#sheet") and "Diagnostics" in pg.inner_text("#sheet .sh-title")
    pg.click("#sheet .sh-close"); pg.click("#menuBtn"); pg.click("#menu #layersBtn"); mis = pg.is_visible("#layers")
    pg.click("#chatBtn"); chatOpen = pg.is_visible("#chat")
    check(chat and mb and fits and "diagBtn" in menu and "layersBtn" in menu and diag and mis and chatOpen and not errs,
          "1 phone: header %s (fits %s, Chat in view %s, ☰ %s); under ☰ %s; Diag %s, Layers %s, Chat opens %s; errors %s" % (hdr, fits, chat, mb, menu, diag, mis, chatOpen, errs))
    pd = br.new_page(viewport={"width": 1400, "height": 800}); up(pd)
    hdr = pd.evaluate("[...document.querySelectorAll('header button')].filter(b=>!b.hidden).map(b=>b.id)")
    check("menuBtn" not in hdr and "diagBtn" in hdr and "chatBtn" in hdr and pd.evaluate("document.querySelectorAll('#menu button').length") == 0,
          "2 desktop: header %s, no ☰" % hdr)
    br.close()
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS)); sys.exit(1 if FAILS else 0)
