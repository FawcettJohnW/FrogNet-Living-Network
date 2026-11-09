#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
# test_connect_page.py -- John's four changes of 2026-11-09 to the phone's page (ui/), in Chromium, the Android side stood
# in for by a recording rtNative:
#   1 [JOINED_IS_DONE_V1] a phone that has joined (cfg.enrolled) opens on the map: no picker, no connect screen; it
#     connects as what joining made it (Team Echo, its user, the node it joined through), no password
#   2 that connect refused: the connect screen, saying why (not a blank screen); the scan is not offered to it
#   3 [LEAD_HOTSPOT_V1] a lead's connect makes sure its hotspot is on (rtNative.ensureHotspot); a member's does not
#   4 [APP_EXIT_V1] Exit, on the connect screen and in the header (☰ on a phone), calls rtNative.exit; with no exit on
#     the native side (the desktop) there is none
#   5 [ONE_DIALOG_V1] Layers, the chat, a sheet (Sensors, Diag) and the person pop-up: opening one closes the other
#   6 [LEGEND_IS_THE_MAP_V1] the legend is what the map draws: a team that joins after the EOC is up comes in; a team
#     whose last position expired, one switched off in Layers, and (a member's map) the teams it does not draw are not in it
import json, os, sys
from playwright.sync_api import sync_playwright
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(HERE)
sys.path.insert(0, HERE); import header_menu  # noqa: E402,F401  [RT_HEADER_MENU_V1]
FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)
NATIVE = """window.__sent = []; window.rtNative = { send: l => __sent.push(['send', JSON.parse(l)]), log() {}, scanTeam: () => __sent.push(['scan']),
  enroll: j => __sent.push(['enroll', JSON.parse(j)]), teamMembers() {}, teamHost() {}, teamPlace() {},
  ensureHotspot: () => __sent.push(['hotspot']), exit: () => __sent.push(['exit']) }"""
DESK = "window.__sent = []; window.rtNative = { send: l => __sent.push(['send', JSON.parse(l)]), log() {} }"
def page(br, stub, cfg, w=420):
    pg = br.new_page(viewport={"width": w, "height": 860}); errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.add_init_script(stub); pg.goto("file://" + os.path.join(APP, "ui", "index.html"))
    pg.evaluate("c => rtInit(c)", dict({"dev": "p1", "name": "Pixel 7", "door": "", "role": "team", "team": "Alpha", "platform": "android", "lock": "",
                                        "build": "test", "invited": "", "lan": "", "enrolled": {}, "layers": []}, **cfg))
    return pg, errs
def dl(pg, o): pg.evaluate("l => rtDeliver(l)", json.dumps(o))
def sent(pg, kind): return [x for x in pg.evaluate("__sent") if x[0] == kind]
def P(team, name, lat): return {"kind": "team", "team": team, "name": name, "lat": lat, "lng": -122.4}
LEGEND = "document.querySelector('#legend').textContent"

with sync_playwright() as pw:
    br = pw.chromium.launch()
    # 1, 3 a joined lead
    pg, errs = page(br, NATIVE, {"enrolled": {"user": "KJ7ABC", "role": "echo", "lead": True, "door": "10.170.170.1"}})
    pg.wait_for_timeout(2300)
    conn = [x[1] for x in sent(pg, "send") if x[1].get("op") == "connect"]; c = conn[-1] if conn else {}
    shown = pg.evaluate("[!document.querySelector('#connect').hidden, !!document.querySelector('#pick') && !document.querySelector('#pick').hidden]")
    dl(pg, {"ev": "status", "text": "[APP_IS_SELF_CONTAINED_V1] waiting for the FrogNet tunnel"})
    splash = pg.evaluate("[!document.querySelector('#splash').classList.contains('gone'), document.querySelector('#splash').innerText]")
    check(len(conn) == 1 and c.get("role") == "team" and c.get("team") == "Echo" and c.get("user") == "KJ7ABC" and c.get("door") == "10.170.170.1"
          and c.get("name") == "KJ7ABC" and c.get("password") == "" and shown == [False, False] and splash[0] and "KJ7ABC: connecting as Team, lead of Echo" in splash[1]
          and "waiting for the FrogNet tunnel" in splash[1] and not errs,
          "1 a joined phone connects by itself, as %s; connect screen / picker shown %s; the splash up, saying %r; errors %s" % ({k: c.get(k) for k in ("role", "team", "user", "door", "name")}, shown, splash, errs))
    dl(pg, {"ev": "connected", "role": "team", "team": "Echo", "build": "t"})
    gone = pg.evaluate("document.querySelector('#splash').classList.contains('gone')")
    check(sent(pg, "hotspot") == [["hotspot"]] and not pg.is_hidden("#main") and gone, "3 the lead's connect asks for its hotspot: %s; the splash gone %s" % (sent(pg, "hotspot"), gone))
    # 2 a joined member whose connect is refused; 3 a member asks for no hotspot
    pg2, errs2 = page(br, NATIVE, {"enrolled": {"user": "W7NEW", "role": "echo", "lead": False, "door": "10.170.170.1"}})
    pg2.wait_for_timeout(2300)
    dl(pg2, {"ev": "error", "text": "the node at 10.170.170.1 is not reachable"})
    check(pg2.is_visible("#connect") and "not reachable" in pg2.inner_text("#cstatus") and pg2.is_visible("[name=door]") and not pg2.is_visible("#scanTeam")
          and not pg2.is_visible("#joinEoc") and not errs2,
          "2 refused: the connect screen says %r; Node shown %s; no scan %s, no Start a team %s" % (pg2.inner_text("#cstatus"), pg2.is_visible("[name=door]"),
                                                                                                   not pg2.is_visible("#scanTeam"), not pg2.is_visible("#joinEoc")))
    pg2.click("#connect button[type=submit]"); dl(pg2, {"ev": "connected", "role": "team", "team": "Echo", "build": "t"})
    check(sent(pg2, "hotspot") == [], "3 a member's connect asks for no hotspot: %s" % sent(pg2, "hotspot"))
    # 4 Exit: on the connect screen of a phone that has not joined, then in the header
    pg3, errs3 = page(br, NATIVE, {})
    pg3.wait_for_selector("#pick:not([hidden])", timeout=5000); pg3.click("[data-pick=team]")
    onConnect = pg3.is_visible("#cexit"); pg3.click("#cexit")
    dl(pg3, {"ev": "connected", "role": "team", "team": "Alpha", "build": "t"}); pg3.wait_for_timeout(200)
    inHeader = pg3.evaluate("!document.querySelector('#exitBtn').hidden")
    if not pg3.is_visible("#exitBtn"): pg3.click("#menuBtn")
    pg3.click("#exitBtn")
    pd, errsd = page(br, DESK, {"platform": ""}, 1280)
    pd.wait_for_selector("#pick:not([hidden])", timeout=5000); pd.click("[data-pick=team]")
    deskNone = not pd.is_visible("#cexit"); dl(pd, {"ev": "connected", "role": "team", "team": "Alpha", "build": "t"}); deskNone = deskNone and pd.evaluate("document.querySelector('#exitBtn').hidden")
    check(onConnect and inHeader and sent(pg3, "exit") == [["exit"], ["exit"]] and deskNone and not errs3 and not errsd,
          "4 Exit on the connect screen %s and in the header %s: rtNative.exit %d times; none on the desktop %s" % (onConnect, inHeader, len(sent(pg3, "exit")), deskNone))
    # 5 one dialog at a time (the desktop's wide header: every button in it)
    vis = lambda: pd.evaluate("['#layers', '#chat', '#sheet', '#pop'].filter(s => !document.querySelector(s).hidden)")
    steps = []
    pd.click("#layersBtn"); steps.append(("Layers", vis()))
    pd.click("#sensBtn"); steps.append(("Sensors", vis()))
    pd.click("#chatBtn"); steps.append(("Chat", vis()))
    pd.click("#diagBtn"); steps.append(("Diag", vis()))
    dl(pd, {"ev": "world", "who": {"b1": {"name": "Bo", "team": "Alpha"}}, "pos": {"b1": P("Alpha", "Bo", 47.5)}})
    pd.evaluate("openPop('b1')"); steps.append(("person", vis()))
    pd.click("#layersBtn"); steps.append(("Layers", vis()))
    pd.click("#layersBtn"); steps.append(("Layers again", vis()))
    want = [["#layers"], ["#sheet"], ["#chat"], ["#sheet"], ["#pop"], ["#layers"], []]
    check([v for _, v in steps] == want and pd.evaluate("S.sheet") == "" and not errsd, "5 one at a time: %s" % steps)
    # 6 the legend is the map: the EOC's, then a member's
    pe, errse = page(br, DESK, {"platform": ""}, 1280)
    pe.wait_for_selector("#pick:not([hidden])", timeout=5000); pe.click("[data-pick=eoc]")
    dl(pe, {"ev": "connected", "role": "eoc", "team": "", "build": "t"})
    dl(pe, {"ev": "world", "who": {}, "pos": {"a1": P("alpha", "Al", 47.5)}}); l1 = pe.evaluate(LEGEND)
    dl(pe, {"ev": "world", "who": {}, "pos": {"a1": P("alpha", "Al", 47.5), "e1": P("Echo", "Ed", 47.6)}}); l2 = pe.evaluate(LEGEND)
    dl(pe, {"ev": "world", "who": {}, "pos": {"a1": P("alpha", "Al", 47.5)}}); l3 = pe.evaluate(LEGEND)      # Echo gone: its last known position
    pe.evaluate("S.lkp = {}; redraw()"); l4 = pe.evaluate(LEGEND)                                           # ... expired
    pe.click("#layersBtn"); pe.locator("#layers label", has_text="Alpha").locator("input").uncheck(); l5 = pe.evaluate(LEGEND)
    pm, errsm = page(br, DESK, {"platform": ""}, 1280)
    pm.wait_for_selector("#pick:not([hidden])", timeout=5000); pm.click("[data-pick=team]")
    dl(pm, {"ev": "connected", "role": "team", "team": "Charlie", "build": "t"})
    dl(pm, {"ev": "world", "who": {}, "pos": {"a1": P("alpha", "Al", 47.5), "c1": P("Charlie", "Cy", 47.55)}}); l6 = pm.evaluate(LEGEND)
    check([l1, l2, l3, l4, l5, l6] == ["Alpha", "AlphaEcho", "AlphaEcho", "Alpha", "", "Charlie"] and not errse and not errsm,
          "6 the legend: Alpha %r; Echo joins %r; Echo's last known position %r; expired %r; Alpha off %r; Charlie's member %r" % (l1, l2, l3, l4, l5, l6))
    br.close()
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS)); sys.exit(1 if FAILS else 0)
