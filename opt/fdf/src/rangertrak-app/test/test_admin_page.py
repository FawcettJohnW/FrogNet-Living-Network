#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_admin_page.py [SHOTS_DIR] -- [CATEGORY_DIALOG_V1] [QR_SAVE_V1] the admin page (ui-admin/) by itself, in Chromium, its
# core's lines given to it (rtDeliver) and its lines to the core read back (rtNative.send):
#   1 the categories are cards (Teams: alpha, bravo, eoc; its users); no Other RAMs, no user Group dropdown
#   2 Add category Power opens its pop-up; Add group crew1 with its lead pat and pat's password: the pop-up shows crew1
#     and pat as lead; Apply sends crew1 (Power, own RAM) and pat (crew1, lead) with pat's password
#   3 in the pop-up, a user added to a group (lou, crew1, a password, lead) and an existing user's lead box: the apply line
#     says so; a new user with no password is not added
#   4 a group removed: its users are under "Users with no group", moved to another group from there
#   5 the QR's Save image: rtNative.saveFile gets a PNG named for the user, which zxing reads as the user's enrollment line
import base64, json, os, sys
from playwright.sync_api import sync_playwright
HERE = os.path.dirname(os.path.abspath(__file__)); APP = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import zxing_read  # noqa: E402
zxing_read.ready()
SHOTS = sys.argv[1] if len(sys.argv) > 1 else ""
FAILS = []
def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what, flush=True)
    if not ok: FAILS.append(what)

V = {"kdf": "pbkdf2-sha256", "n": 1, "salt": "00", "hash": "00"}
CUR = {"seq": 5, "rams": ["general", "alpha", "bravo"], "partition": "relocate", "login": False,
       "roles": {"eoc": {"default": False, "category": "Teams"}, "alpha": {"default": True, "category": "Teams"}, "bravo": {"default": True, "category": "Teams"}},
       "categories": ["Teams"], "users": {"EOCAdmin": {"role": "eoc", "verifier": V}, "kim": {"role": "alpha", "lead": True, "verifier": V}}, "select": {}}
STUB = """window.SENT = []; window.SAVED = null;
window.rtNative = { send(l) { SENT.push(JSON.parse(l)) }, saveFile(n, b) { SAVED = [n, b] } }"""
with sync_playwright() as pw:
    br = pw.chromium.launch(); pg = br.new_context(viewport={"width": 1280, "height": 900}).new_page()
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.add_init_script(STUB)
    pg.goto("file://" + os.path.join(APP, "ui-admin", "index.html"))
    deliver = lambda o: pg.evaluate("(l) => rtDeliver(l)", json.dumps(o))
    pg.evaluate("rtInit({door: '10.170.170.1', app: 'rangertrak', user: 'boss'})")
    deliver({"ev": "connected", "app": "rangertrak", "user": "boss", "door": "10.170.170.1", "build": "test"})
    deliver({"ev": "state", "current": CUR, "configs": [], "applied": [], "served": {}})
    last = lambda: pg.evaluate("SENT.filter(m => m.op === 'apply').pop() || null")
    # 1
    cards = pg.evaluate("[...document.querySelectorAll('#cats .catcard')].map(b => b.innerText)")
    check(len(cards) == 1 and "Teams" in cards[0] and "3 groups" in cards[0] and "2 users" in cards[0] and not pg.query_selector("#extraRams")
          and not pg.query_selector("[data-u]") and not errs, "1 cards %s; no Other RAMs, no Group dropdown; page errors %s" % (cards, errs))
    # 2
    pg.fill("#newCat", "Power"); pg.click("#addCat")
    opened = pg.is_visible("#cat") and pg.inner_text("#catTitle") == "Power"
    pg.fill("#newRole", "crew1"); pg.fill("#newLead", "pat"); pg.fill("#newLeadPw", "pat-2026"); pg.click("#addRole")
    grp = pg.inner_text("#catGroups")
    lead_on = pg.is_checked("[data-lead=pat]")
    if SHOTS: pg.screenshot(path=os.path.join(SHOTS, "admin-cat.png"))
    pg.click("#catApply"); a = last() or {}
    c = a.get("config", {})
    check(opened and "crew1" in grp and lead_on and c.get("roles", {}).get("crew1") == {"default": False, "category": "Power"} and "crew1" in c.get("rams", [])
          and c.get("users", {}).get("pat") == {"role": "crew1", "lead": True} and a.get("passwords") == {"pat": "pat-2026"} and c.get("categories") == ["Teams", "Power"],
          "2 Power's pop-up opened %s; crew1 with lead pat (box %s); applied: roles.crew1 %s, users.pat %s, passwords %s, categories %s"
          % (opened, lead_on, c.get("roles", {}).get("crew1"), c.get("users", {}).get("pat"), list((a.get("passwords") or {}).keys()), c.get("categories")))
    # the members adopted it
    cur2 = json.loads(json.dumps(CUR)); cur2.update(seq=6, rams=c["rams"], roles=c["roles"], categories=c["categories"])
    cur2["users"]["pat"] = {"role": "crew1", "lead": True, "verifier": V}
    deliver({"ev": "state", "current": cur2, "configs": [], "applied": [], "served": {}})
    # 3
    pg.fill("[data-nu=crew1][data-f=name]", "lou"); pg.click("[data-add-user=crew1]")
    nopw = not pg.query_selector("[data-pw=lou]") and "lou: give its password" in pg.inner_text("#catChanges")
    pg.fill("[data-nu=crew1][data-f=pw]", "lou-2026"); pg.check("[data-nu=crew1][data-f=lead]"); pg.click("[data-add-user=crew1]")
    pg.uncheck("[data-lead=pat]")
    pg.click("#catApply"); a = last() or {}; u = a.get("config", {}).get("users", {})
    check(nopw and u.get("lou") == {"role": "crew1", "lead": True} and u.get("pat") == {"role": "crew1", "lead": False} and a.get("passwords") == {"lou": "lou-2026"},
          "3 lou with no password held (%s); applied: lou %s, pat %s, passwords %s" % (nopw, u.get("lou"), u.get("pat"), list((a.get("passwords") or {}).keys())))
    cur3 = json.loads(json.dumps(cur2)); cur3["seq"] = 7; cur3["users"].update(lou={"role": "crew1", "lead": True, "verifier": V}, pat={"role": "crew1", "lead": False, "verifier": V})
    deliver({"ev": "state", "current": cur3, "configs": [], "applied": [], "served": {}})
    # 4
    pg.click("[data-del-role=crew1]"); pg.click("#catClose")
    lost = pg.inner_text("#lost")
    pg.select_option("[data-move=pat]", "alpha"); pg.click("[data-del-user=lou]")
    pg.click("[data-open-cat=Power]"); pg.click("#catDel")
    pg.click("#apply"); a = last() or {}; c = a.get("config", {})
    check("pat" in lost and "lou" in lost and c.get("users", {}).get("pat", {}).get("role") == "alpha" and "lou" not in c.get("users", {})
          and "crew1" not in c.get("roles", {}) and c.get("categories") == ["Teams"],
          "4 no group: %r; applied: pat %s, lou gone %s, categories %s" % (lost.replace("\n", " ")[:80], c.get("users", {}).get("pat"), "lou" not in c.get("users", {}), c.get("categories")))
    # 5
    pg.click("[data-open-cat=Teams]"); pg.click("[data-qr=kim]"); pg.wait_for_selector("#qr:not([hidden]) #qrImg svg")
    pg.click("#qrSave"); pg.wait_for_function("SAVED !== null", timeout=5000)
    name, b64 = pg.evaluate("SAVED")
    f = os.path.join("/tmp", name); open(f, "wb").write(base64.b64decode(b64))
    got = zxing_read.read([f]).get(f)
    want = "fdfenroll1 http://10.170.170.1/dc/ram.php rangertrak kim"
    if SHOTS: open(os.path.join(SHOTS, name), "wb").write(base64.b64decode(b64))
    check(name == "RangerTrak-rangertrak-kim.png" and got == want and not errs, "5 saved %s (%d bytes): reads %r; page errors %s" % (name, len(b64) * 3 // 4, got, errs))
    br.close()
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS)); sys.exit(1 if FAILS else 0)
