#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_kinds_page.py -- [MAP_KINDS_DISCOVERED_V1] the map draws every kind that is connected, not only the kinds its
# layers know (John 2026-11-06: "Admin apps should show connected children of all types. Maps too"), in the EOC's view
# (every team, every kind) and a team member's:
#   1 a world with a kind no layer knows (drone) and a team that joined the EOC (Red Cross): both drawn; Drone in Layers
#     (on) and in the legend; Red Cross in Layers' teams and the legend; Ambulance (no ambulance on the network) not in it
#   2 Drone off in Layers: its marker is gone; on again: back
#   3 a team member's view (team Charlie): the drone is drawn too (a found kind is on)
import json, os, sys
from playwright.sync_api import sync_playwright
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(HERE)
sys.path.insert(0, HERE); import header_menu  # noqa: E402,F401  [RT_HEADER_MENU_V1]
FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)
STUB = "window.SENT=[]; window.rtNative={send(l){SENT.push(JSON.parse(l))}, log(){}, teamMembers(){}, teamHost(){}, teamPlace(){}}"
POS = {"d1": {"kind": "drone", "name": "Drone 1", "lat": 47.45, "lng": -122.46},
       "r1": {"kind": "team", "team": "Red Cross", "name": "Dan", "lat": 47.46, "lng": -122.47},
       "c1": {"kind": "team", "team": "Charlie", "name": "Cat", "lat": 47.47, "lng": -122.45}}
def up(pg, role, team):
    pg.add_init_script(STUB); pg.goto("file://" + os.path.join(APP, "ui", "index.html"))
    dl = lambda o: pg.evaluate("l => rtDeliver(l)", json.dumps(o))
    pg.evaluate("c => rtInit(c)", {"dev": "p1", "name": "Pixel", "door": "10.170.170.1", "role": role, "team": team, "platform": "android", "layers": [], "enrolled": {}})
    pg.wait_for_selector("#pick:not([hidden])"); pg.click("[data-pick=%s]" % ("eoc" if role == "eoc" else "team"))
    dl({"ev": "connected", "role": role, "view": role, "team": team, "name": "Pixel", "dev": "p1", "door": "10.170.170.1", "build": "t"})
    dl({"ev": "mission", "id": "standing", "name": "Standing operations", "bag": {"name": "Standing operations"}})
    dl({"ev": "world", "who": {k: {"name": v["name"]} for k, v in POS.items()}, "pos": POS})
    pg.wait_for_timeout(300)
    return dl
drawn = "id => !!S.markers[id]"
with sync_playwright() as pw:
    br = pw.chromium.launch(); errs = []
    pg = br.new_page(viewport={"width": 1400, "height": 900}); pg.on("pageerror", lambda e: errs.append(str(e)))
    up(pg, "eoc", "")
    lay = pg.evaluate("document.querySelector('#layers').textContent"); leg = pg.inner_text("#legend")
    on = pg.evaluate("[...document.querySelectorAll('#layers label')].find(l => l.textContent.trim() === 'Drone')?.querySelector('input').checked")
    check(pg.evaluate(drawn, "d1") and pg.evaluate(drawn, "r1") and on and "Drone" in leg and "Red Cross" in lay and "Red Cross" in leg and "Ambulance" not in leg and not errs,
          "1 the EOC: drone drawn %s, Red Cross drawn %s; Layers: Drone on %s, Red Cross %s; legend %r; errors %s"
          % (pg.evaluate(drawn, "d1"), pg.evaluate(drawn, "r1"), on, "Red Cross" in lay, leg, errs))
    pg.evaluate("$('#layers').hidden = false")
    box = pg.locator("#layers label", has_text="Drone").locator("input")
    box.uncheck(); gone = not pg.evaluate(drawn, "d1"); box.check(); back = pg.evaluate(drawn, "d1")
    check(gone and back, "2 Drone off in Layers: gone %s; on: back %s" % (gone, back))
    pg2 = br.new_page(viewport={"width": 1400, "height": 900}); pg2.on("pageerror", lambda e: errs.append(str(e)))
    up(pg2, "team", "Charlie")
    check(pg2.evaluate(drawn, "d1") and pg2.evaluate(drawn, "c1") and not errs,
          "3 team Charlie's member: the drone drawn %s, Charlie drawn %s; errors %s" % (pg2.evaluate(drawn, "d1"), pg2.evaluate(drawn, "c1"), errs))
    br.close()
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS)); sys.exit(1 if FAILS else 0)
