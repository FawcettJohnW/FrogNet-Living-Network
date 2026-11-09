#!/usr/bin/env python3
################################################################
#  Copyright (C) 2016-2026 Fawcett Innovations LLC             #
#  SPDX-License-Identifier: GPL-2.0-only                       #
################################################################
# test_admin_page.py [SHOTS_DIR] -- [TEAMS_AUTHORIZED_V1] [QR_SAVE_V1] the admin page (ui-admin/) by itself, in Chromium, its
# core's lines given to it (rtDeliver) and its lines to the core read back (rtNative.send):
#   1 (John 2026-11-09: "we are authorizing Teams, and that's the trust boundary"; the EOC "does not allocate" categories)
#     Teams: alpha (its login kim), bravo (no login yet); EOC users: EOCAdmin; no categories, no groups' user lists, no
#     EOC teams password
#   2 Add Team echo with its password: Apply sends echo (no category: its app's, at its claim; its own RAM) and its
#     login echo (echo's lead) with the password; a Team with no password is not added
#   3 Add EOC user dana: a user of the EOC's role; a configuration with no EOC role gets one (eoc, category EOC)
#   4 a Team removed goes with its login (alpha, kim); bravo given a password gets its login (bravo, lead)
#   5 the QR's Save image: rtNative.saveFile gets a PNG named for the login, which zxing reads as its enrollment line
#   6 who is on now, in its group's row; one in no group said so
#   7 [CHILDREN_JOIN_V1][ADMIN_TREE_V1] a team that joined the EOC by the old teams password (Red Cross) is a group (its
#     row, "joined"); Connected now is a tree: the mission, EOC, Teams, alpha with Pat, Red Cross with Dan (team) and a
#     drone (a kind no list here has); in no group, Win PC
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
       "roles": {"eoc": {"default": False, "category": "EOC"}, "alpha": {"default": True, "category": "Teams"}, "bravo": {"default": True, "category": "Teams"}},
       "categories": ["EOC", "Teams"], "users": {"EOCAdmin": {"role": "eoc", "verifier": V}, "kim": {"role": "alpha", "lead": True, "verifier": V}}, "select": {}}
STUB = """window.SENT = []; window.SAVED = null;
window.rtNative = { send(l) { SENT.push(JSON.parse(l)) }, saveFile(n, b) { SAVED = [n, b] } }"""
with sync_playwright() as pw:
    br = pw.chromium.launch(); pg = br.new_context(viewport={"width": 1280, "height": 900}).new_page()
    errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.add_init_script(STUB)
    pg.goto("file://" + os.path.join(APP, "ui-admin", "index.html"))
    deliver = lambda o: pg.evaluate("(l) => rtDeliver(l)", json.dumps(o))
    pg.evaluate("rtInit({door: '10.170.170.1', app: 'openeoc', user: 'boss'})")
    deliver({"ev": "connected", "app": "openeoc", "user": "boss", "door": "10.170.170.1", "build": "test"})
    deliver({"ev": "state", "current": CUR, "configs": [], "applied": [], "served": {}})
    last = lambda: pg.evaluate("SENT.filter(m => m.op === 'apply').pop() || null")
    rowsOf = lambda sel: pg.evaluate("s => [...document.querySelectorAll(s + ' tr')].map(t => t.innerText.replace(/\\s+/g, ' ').trim())", sel)
    # 1
    teams, eoc = rowsOf("#teams"), rowsOf("#eocUsers")
    gone = [x for x in ("#cats", "#addCat", "#newCat", "#cat", "#joinPw", "#joinQr", "#catGroups") if pg.query_selector(x)]
    check(len(teams) == 2 and teams[0].startswith("alpha") and "login kim" in teams[0] and "Teams" in teams[0] and teams[1].startswith("bravo")
          and pg.query_selector("[data-tpw=bravo]") and len(eoc) == 1 and eoc[0].startswith("EOCAdmin") and not gone and not errs,
          "1 Teams %s; EOC users %s; gone from the page: still there %s; page errors %s" % (teams, eoc, gone, errs))
    # 2
    pg.fill("#newTeam", "echo"); pg.click("#addTeam"); nopw = "echo: give its password" in pg.inner_text("#teamSays") and "echo" not in pg.inner_text("#teams")
    pg.fill("#newTeam", "Echo"); pg.fill("#newTeamPw", "echo-2026"); pg.click("#addTeam")
    if SHOTS: pg.screenshot(path=os.path.join(SHOTS, "admin-teams.png"), full_page=True)
    pg.click("#apply"); a = last() or {}; c = a.get("config", {})
    check(nopw and c.get("roles", {}).get("echo") == {"default": False, "category": ""} and "echo" in c.get("rams", []) and c.get("users", {}).get("echo") == {"role": "echo", "lead": True}
          and a.get("passwords") == {"echo": "echo-2026"} and c.get("categories") == ["EOC", "Teams"] and "join" not in c,
          "2 echo with no password held (%s); applied: roles.echo %s, users.echo %s, passwords %s, categories %s"
          % (nopw, c.get("roles", {}).get("echo"), c.get("users", {}).get("echo"), list((a.get("passwords") or {}).keys()), c.get("categories")))
    cur2 = json.loads(json.dumps(CUR)); cur2.update(seq=6, rams=c["rams"], roles=c["roles"], categories=c["categories"])
    cur2["users"]["echo"] = {"role": "echo", "lead": True, "verifier": V}
    deliver({"ev": "state", "current": cur2, "configs": [], "applied": [], "served": {}})
    # 3
    pg.fill("#newEoc", "dana"); pg.click("#addEoc"); nopw = "dana: give its password" in pg.inner_text("#eocSays")
    pg.fill("#newEoc", "dana"); pg.fill("#newEocPw", "dana-2026"); pg.click("#addEoc")
    pg.click("#apply"); a = last() or {}; u = a.get("config", {}).get("users", {})
    noEoc = json.loads(json.dumps(CUR)); del noEoc["roles"]["eoc"]; del noEoc["users"]["EOCAdmin"]; noEoc["categories"] = ["Teams"]
    deliver({"ev": "state", "current": noEoc, "configs": [], "applied": [], "served": {}})
    pg.fill("#newEoc", "lee"); pg.fill("#newEocPw", "lee-2026"); pg.click("#addEoc"); pg.click("#apply"); b3 = (last() or {}).get("config", {})
    check(nopw and u.get("dana") == {"role": "eoc", "lead": False} and a.get("passwords") == {"dana": "dana-2026"}
          and b3.get("roles", {}).get("eoc") == {"default": False, "category": "EOC"} and b3.get("users", {}).get("lee") == {"role": "eoc", "lead": False} and "eoc" not in b3.get("rams", []),
          "3 dana with no password held (%s); applied: dana %s; with no EOC role: eoc %s, lee %s" % (nopw, u.get("dana"), b3.get("roles", {}).get("eoc"), b3.get("users", {}).get("lee")))
    deliver({"ev": "state", "current": cur2, "configs": [], "applied": [], "served": {}})
    # 4
    pg.click("[data-del-team=alpha]"); pg.fill("[data-tpw=bravo]", "bravo-2026"); pg.dispatch_event("[data-tpw=bravo]", "change")
    pg.click("#apply"); a = last() or {}; c = a.get("config", {})
    check("alpha" not in c.get("roles", {}) and "kim" not in c.get("users", {}) and "alpha" not in c.get("rams", []) and c.get("users", {}).get("bravo") == {"role": "bravo", "lead": True}
          and a.get("passwords") == {"bravo": "bravo-2026"},
          "4 applied: alpha gone %s, kim gone %s; bravo's login %s, passwords %s" % ("alpha" not in c.get("roles", {}), "kim" not in c.get("users", {}), c.get("users", {}).get("bravo"), list((a.get("passwords") or {}).keys())))
    deliver({"ev": "state", "current": CUR, "configs": [], "applied": [], "served": {}})
    # 5
    pg.click("[data-qr=kim]"); pg.wait_for_selector("#qr:not([hidden]) #qrImg svg")
    pg.click("#qrSave"); pg.wait_for_function("SAVED !== null", timeout=5000)
    name, b64 = pg.evaluate("SAVED")
    f = os.path.join("/tmp", name); open(f, "wb").write(base64.b64decode(b64))
    got = zxing_read.read([f]).get(f)
    want = "fdfenroll1 http://10.170.170.1/dc/ram.php openeoc kim"
    if SHOTS: open(os.path.join(SHOTS, name), "wb").write(base64.b64decode(b64))
    check(name == "OpenEOC-openeoc-kim.png" and got == want and not errs, "5 saved %s (%d bytes): reads %r; page errors %s" % (name, len(b64) * 3 // 4, got, errs))
    # 5b [EOC_USER_QR_V1] an EOC user's QR: for the EOC app, its line the user's
    pg.click("#qrClose"); pg.click("[data-qr=EOCAdmin]"); pg.wait_for_selector("#qr:not([hidden]) #qrImg svg")
    wq, lq = pg.inner_text("#qrWho"), pg.inner_text("#qrLine")
    check("EOC user" in wq and "the EOC app scans this" in wq and lq == "fdfenroll1 http://10.170.170.1/dc/ram.php openeoc EOCAdmin",
          "5b an EOC user's QR: %r · %r" % (wq, lq))
    pg.click("#qrClose")
    # 6 [ADMIN_ON_NOW_V1] who is on now, in its group's row; one in no group said so
    deliver({"ev": "onNow", "mission": "standing", "who": {"pa": {"name": "Pat", "kind": "team", "team": "Alpha", "via": "10.170.170.1"},
                                                          "px": {"name": "Win PC", "kind": "team", "team": "Zulu"}}})
    row = pg.evaluate("[...document.querySelectorAll('#groups tr')].map(t=>t.innerText).find(t=>/^alpha/.test(t)) || ''")
    on = pg.inner_text("#onNow")
    check("Pat" in row and "2 on now" in on and "Win PC" in on and "no such group" in on, "6 alpha's row: %r; %r" % (row.replace("\t", " | ")[:120], on[:160]))
    # 7 a team that joined, and the tree of who is connected
    deliver({"ev": "state", "current": CUR, "configs": [], "applied": [], "served": {}, "children": {"Red Cross": {"name": "Red Cross", "kind": "team", "at": 1793900000, "from": "10.170.170.40"}}})
    deliver({"ev": "onNow", "mission": "standing", "who": {"pa": {"name": "Pat", "kind": "team", "team": "Alpha", "via": "10.170.170.1"},
                                                          "dn": {"name": "Dan", "kind": "team", "team": "Red Cross"}, "dr": {"name": "Drone 1", "kind": "drone", "unit": "red cross"},
                                                          "px": {"name": "Win PC", "kind": "team", "team": "Zulu"}}})
    rc = pg.evaluate("[...document.querySelectorAll('#groups tr')].map(t=>t.innerText).find(t=>/^Red Cross/.test(t)) || ''")
    tr = pg.evaluate("""(() => { const li = [...document.querySelectorAll('#onTree li')]; const f = (t) => li.find(l => l.firstElementChild && l.firstElementChild.textContent === t);
      const kids = (l) => l ? [...l.querySelectorAll(':scope > ul > li > b')].map(b => b.textContent) : [];
      return { top: kids(document.querySelector('#onTree > ul > li')), teams: kids(f('Teams')), alpha: kids(f('alpha')), rc: kids(f('Red Cross')), out: kids(f('In no group')),
               drone: (f('Drone 1') || {}).textContent || '' } })()""")
    if SHOTS: pg.locator("#onTree").screenshot(path=os.path.join(SHOTS, "admin-tree.png"))
    check("joined" in rc and "Dan" in rc and tr["top"] == ["EOC", "Teams", "In no group"] and tr["teams"] == ["alpha", "bravo", "Red Cross"]
          and tr["alpha"] == ["Pat"] and sorted(tr["rc"]) == ["Dan", "Drone 1"] and "drone" in tr["drone"] and tr["out"] == ["Win PC"] and not errs,
          "7 Red Cross's row %r; the tree %s; errors %s" % (rc.replace("\t", " | ")[:140], tr, errs))
    br.close()
print("RESULT", "PASS" if not FAILS else "FAIL: %d" % len(FAILS)); sys.exit(1 if FAILS else 0)
