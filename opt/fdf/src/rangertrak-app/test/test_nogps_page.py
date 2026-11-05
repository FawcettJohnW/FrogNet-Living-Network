#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_nogps_page.py -- [RT_NO_GPS_V1] [RT_AT_ITS_NODE_V1] a computer (no GPS) in the page, as the desktop app now runs it
# (ev nogps at load): its core's lines given (rtDeliver), its lines to the core read back (rtNative.send):
#   1 connected, with no address saved: it is at its node (the door says Seattle7; its GPS sensor's address is not the
#     door's: every node's LAN door is 10.170.170.1) -- op fix src node --
#     and asks Where are you?
#   2 "47.5, -122.3" typed: op fix src address, and the address saved (rtNative.saveAddress)
#   3 the next start with that address saved: op fix src address straight away, no question
#   4 a door that does not say its node: not on the map, and Where are you? says why (the nodes with a fix named)
import json, os, sys
from playwright.sync_api import sync_playwright
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(HERE)
FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)
STUB = "window.SENT=[]; window.SAVED=null; window.rtNative={send(l){SENT.push(JSON.parse(l))}, saveAddress(j){SAVED=JSON.parse(j)}, log(){}}"
HOSTS = {"ev": "hosts", "list": [{"name": "Seattle7", "address": "10.188.188.7", "lat": 47.4473, "lng": -122.4597},
                                 {"name": "Seattle5", "address": "10.170.170.5", "lat": 47.61, "lng": -122.33}], "nofix": []}
def start(pg, saved, node="Seattle7"):
    pg.add_init_script(STUB); pg.goto("file://" + os.path.join(APP, "ui", "index.html"))
    dl = lambda o: pg.evaluate("l => rtDeliver(l)", json.dumps(o))
    pg.evaluate("rtInit({dev:'pc1',name:'Win PC',door:'10.170.170.1',role:'team',team:'Alpha',platform:'desktop',layers:[]})")
    dl({"ev": "nogps", "why": "This computer has no GPS.", "saved": saved})
    pg.wait_for_selector("#pick:not([hidden])"); pg.click("[data-pick=team]"); pg.click("#connect button[type=submit], #connect .go")
    dl({"ev": "connected", "role": "team", "view": "team", "team": "Alpha", "name": "Win PC", "dev": "pc1", "door": "10.170.170.1", "build": "t", "node": node})
    dl({"ev": "mission", "id": "standing", "name": "Standing operations"})
    dl(HOSTS)
    return dl
fixes = lambda pg: pg.evaluate("SENT.filter(m=>m.op==='fix')")
with sync_playwright() as pw:
    br = pw.chromium.launch(); errs = []
    pg = br.new_page(); pg.on("pageerror", lambda e: errs.append(str(e)))
    start(pg, None)
    f = fixes(pg)
    asked = pg.is_visible("#sheet") and "Where are you" in pg.inner_text("#sheet")
    check(bool(f) and f[-1].get("src") == "node" and abs(f[-1]["lat"] - 47.4473) < 1e-6 and asked and "Seattle7" in pg.inner_text("#sheet"),
          "1 no address: at its node Seattle7 (%s); asked Where are you? %s" % (f[-1] if f else None, asked))
    pg.fill(".af [name=a]", "47.5, -122.3"); pg.click(".af .go")
    f = fixes(pg)
    check(f[-1].get("src") == "address" and f[-1]["lat"] == 47.5 and pg.evaluate("SAVED") == {"lat": 47.5, "lng": -122.3, "label": "47.5, -122.3"},
          "2 typed: %s; saved %s" % (f[-1], pg.evaluate("SAVED")))
    pg2 = br.new_page(); pg2.on("pageerror", lambda e: errs.append(str(e)))
    start(pg2, {"lat": 47.5, "lng": -122.3, "label": "47.5, -122.3"})
    f = fixes(pg2)
    check(bool(f) and all(x.get("src") == "address" for x in f) and not (pg2.is_visible("#sheet") and "Where are you" in pg2.inner_text("#sheet")) and not errs,
          "3 saved: %s, not asked; page errors %s" % (f, errs))
    # 4 [DOOR_SAYS_ITS_NODE_V1] a door that does not say its node (older than dc-door r16): not on the map, and the sheet says why
    pg3 = br.new_page(); pg3.on("pageerror", lambda e: errs.append(str(e)))
    start(pg3, None, node="")
    t = pg3.inner_text("#sheet") if pg3.is_visible("#sheet") else ""
    check(not fixes(pg3) and "does not say which node it is" in t and "Seattle7" in t, "4 no node said: no fix sent; the sheet: %r" % t[:200])
    br.close()
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS)); sys.exit(1 if FAILS else 0)
