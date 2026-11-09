#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
# test_enroll_page.py -- [ENROLL_V1] the phone's page (ui/), in Chromium, with the Android side stood in for by a
# recording rtNative (the native side's events are delivered as MainActivity delivers them):
#   1 [CONNECT_QR_FIRST_V1] (John 2026-11-09) Scan a QR code is first on an Android phone's connect screen; the fill-in
#     fields are below it, under Manual login (closed), with Start a team (Join EOC was its name) in it
#   6 [TEAM_JOIN_V1] the team's QR scanned asks a call sign and the team password; Join enrolls the call sign, the
#     line keeping the lead's key's fingerprint [SEALED_ENROLL_V1] (the team's QR has five parts). [CONNECT_QR_FIRST_V1]
#     6b with Your name typed before (nameGiven) and a call sign, the team password only: the call sign is that name
#   2 a scanned code (enrollScan) asks for that user's password; Join hands the line and the password to the native side
#   3 enrolled (lead of echo, the door): the page connects as that user, Team view, team Echo, the door, the password
#   4b [MEMBER_PASSWORDS_OPTIONAL_V1] one team password by default; a member with their own password, and their own QR, if
#      the lead adds one under Individual passwords
#   4 the lead's Team members button; the members sheet (members): adding a member hands its name and password to the
#     native side; a member's QR, read by zxing (the phone scanner's library), is that member's enrollment line at the
#     address chosen, and choosing another address changes it
#   5 a refused enrollment is said, and Join can be pressed again
#   7 [CHILDREN_JOIN_V1] Start a team, under Manual login: the team's name and the EOC's teams password; Join enrolls the
#     name at the EOC's door (the Node field's), and enrolled (lead of Red Cross) connects as Team Red Cross
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zxing_read  # noqa: E402
from playwright.sync_api import sync_playwright
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import header_menu  # noqa: E402,F401  [RT_HEADER_MENU_V1]

APP = zxing_read.APP
fails = []
def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what); (None if ok else fails.append(what))
zxing_read.ready()

STUB = """window.__sent = []; window.rtNative = {
  send: l => __sent.push(['send', JSON.parse(l)]), scanTeam: () => __sent.push(['scan']),
  enroll: j => __sent.push(['enroll', JSON.parse(j)]), teamMembers: () => __sent.push(['members']),
  addMember: (n, p) => __sent.push(['add', n, p]), removeMember: n => __sent.push(['remove', n]),
  teamHost: () => __sent.push(['host']),
  setTeamPassword: p => __sent.push(['teamPw', p]) }"""
with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
    b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 420, "height": 860})
    pg.add_init_script(STUB)
    pg.goto("file://" + os.path.join(APP, "ui", "index.html"))
    pg.evaluate("rtInit({dev: 'p1', name: 'Pixel', door: '', role: 'team', team: 'Alpha', platform: 'android', lock: '', build: 'test', invited: '', lan: '', enrolled: {}})")
    pg.wait_for_selector("#pick:not([hidden])", timeout=5000); pg.click("[data-pick=team]")   # [RT_APP_PICKER_V1]
    pg.wait_for_selector("#connect:not([hidden])", timeout=5000)
    first = pg.evaluate("!!(document.querySelector('#qrSec').compareDocumentPosition(document.querySelector('#manual')) & Node.DOCUMENT_POSITION_FOLLOWING)")
    closed = not pg.evaluate("$('#manual').open"); hid = not pg.is_visible("#joinEoc") and not pg.is_visible("[name=door]")
    pg.click("#manual > summary"); opened = pg.is_visible("[name=door]") and pg.is_visible("#joinEoc") and pg.inner_text("#joinEoc").startswith("Start a team")
    pg.click("#manual > summary")
    check(pg.is_visible("#scanTeam") and pg.inner_text("#scanTeam") == "Scan a QR code" and first and closed and hid and opened,
          "1 Scan a QR code first (%s), Manual login below it, closed (%s: its fields hidden %s), opened: the fields and Start a team %s" % (first, closed, hid, opened))
    pg.click("#scanTeam")
    line = "fdfenroll1 http://10.170.170.1/dc/ram.php openeoc kim"
    pg.evaluate("l => rtDeliver(JSON.stringify({ev: 'enrollScan', line: l, url: 'http://10.170.170.1/dc/ram.php', app: 'openeoc', user: 'kim'}))", line)
    shown = pg.is_visible("#enrollBox") and "kim" in pg.inner_text("#enrollWho")
    pg.fill("#enrollPw", "kim-2026"); pg.click("#enrollGo")
    sent = pg.evaluate("__sent")
    check(shown and ["scan"] in sent and ["enroll", {"line": line, "password": "kim-2026"}] in sent,
          "2 the scanned code asks for kim's password; Join hands over the line and the password: %s" % [s for s in sent if s[0] == "enroll"])
    pg.evaluate("rtDeliver(JSON.stringify({ev: 'enrolled', user: 'kim', role: 'echo', lead: true, door: '10.170.170.1', invited: 'openeoc-kim in frognet'}))")
    conn = [s[1] for s in pg.evaluate("__sent") if s[0] == "send" and s[1].get("op") == "connect"]
    c = conn[-1] if conn else {}
    check(c.get("door") == "10.170.170.1" and c.get("name") == "kim" and c.get("role") == "team" and c.get("team") == "Echo"
          and c.get("user") == "kim" and c.get("password") == "kim-2026" and c.get("invite") == "",
          "3 enrolled: the page connects as kim, Team Echo, at the door: %s" % {k: c.get(k) for k in ("door", "name", "role", "team", "user")})
    pg.evaluate("rtDeliver(JSON.stringify({ev: 'connected', role: 'team', team: 'Echo', build: 'test'}))")
    btn = pg.evaluate("!document.querySelector('#membersBtn').hidden")   # (under ☰ on a phone: [RT_HEADER_MENU_V1])
    pg.click("#membersBtn")
    addrs = [{"label": "this phone's hotspot", "addr": "192.168.43.1"}, {"label": "FrogNet (the Internet)", "addr": "10.253.1.1"}]
    # [TEAM_JOIN_V1] one QR, one password: no QR until the lead sets the password; then the team's QR, at the address chosen
    pg.evaluate("a => rtDeliver(JSON.stringify({ev: 'members', app: 'openeoc', team: 'echo', members: [], addrs: a, port: 21801, key: 'dW9LoJ0JmymSPLael9M7kg', sponsoring: true, teamPassword: false}))", addrs)
    noQr = pg.locator("#memQr svg").count() == 0 and "set the team password" in pg.inner_text("#memQr")
    pg.fill("#teamPw", "echo-2026"); pg.click("#teamPwSet")
    setPw = ["teamPw", "echo-2026"] in pg.evaluate("__sent")
    pg.evaluate("a => rtDeliver(JSON.stringify({ev: 'members', app: 'openeoc', team: 'echo', members: ['KJ7ABC'], addrs: a, port: 21801, key: 'dW9LoJ0JmymSPLael9M7kg', sponsoring: true, teamPassword: true}))", addrs)
    pg.wait_for_selector("#memQr svg", timeout=3000)
    q1 = os.path.join(tmp, "q1.png"); pg.locator("#memQr svg").screenshot(path=q1)
    pg.select_option("#memAt", "10.253.1.1"); pg.wait_for_function("document.querySelector('#memQr .who').textContent.includes('10.253.1.1')", timeout=3000)
    q2 = os.path.join(tmp, "q2.png"); pg.locator("#memQr svg").screenshot(path=q2)
    got = zxing_read.read([q1, q2])
    w1 = "fdfenroll1 http://192.168.43.1:21801/enroll openeoc * dW9LoJ0JmymSPLael9M7kg"; w2 = "fdfenroll1 http://10.253.1.1:21801/enroll openeoc * dW9LoJ0JmymSPLael9M7kg"   # [SEALED_ENROLL_V1] its key's fingerprint
    joined = "KJ7ABC" in pg.inner_text("#sheet")
    pg.evaluate("rtDeliver(JSON.stringify({ev: 'world', who: {d9: {name: 'Drone 9'}, x1: {name: 'Other'}}, pos: {d9: {kind: 'drone', unit: 'echo', name: 'Drone 9'}, x1: {kind: 'team', team: 'Alpha', name: 'Other'}}}))")
    nowEcho = pg.inner_text("#memNow"); joined = joined and "Drone 9" in nowEcho and "drone" in nowEcho and "Other" not in nowEcho
    check(btn and ["members"] in pg.evaluate("__sent") and noQr and setPw and got.get(q1) == w1 and got.get(q2) == w2 and joined,
          "4 the lead's team: no QR before the password %s; the password handed over %s; the team's QR reads %r, then %r; KJ7ABC listed, on now Drone 9 (drone), not Alpha's %s" % (noQr, setPw, got.get(q1), got.get(q2), joined))
    # 4b [MEMBER_PASSWORDS_OPTIONAL_V1] the team's one password is the default; Individual passwords, closed, is the option
    closed = not pg.evaluate("document.querySelector('#memOwn').open") and not pg.is_visible("#memName")
    pg.click("#memOwn > summary"); pg.fill("#memName", "w7own"); pg.click("#memAdd"); nopw = not [x for x in pg.evaluate("__sent") if x[0] == "add"]
    pg.fill("#memPw", "own-2026"); pg.click("#memAdd"); added = ["add", "W7OWN", "own-2026"] in pg.evaluate("__sent")
    pg.evaluate("a => rtDeliver(JSON.stringify({ev: 'members', app: 'openeoc', team: 'echo', members: ['KJ7ABC', 'W7OWN'], own: ['W7OWN'], addrs: a, port: 21801, key: 'dW9LoJ0JmymSPLael9M7kg', sponsoring: true, teamPassword: true}))", addrs)
    pg.wait_for_selector("#memOwnQr svg", timeout=3000)
    q3 = os.path.join(tmp, "q3.png"); pg.locator("#memOwnQr svg").screenshot(path=q3); got3 = zxing_read.read([q3]).get(q3)
    w3 = "fdfenroll1 http://10.253.1.1:21801/enroll openeoc W7OWN dW9LoJ0JmymSPLael9M7kg"
    check(closed and nopw and added and got3 == w3 and pg.locator("#memQr svg").count() == 1,
          "4b Individual passwords closed by default %s; W7OWN with no password not added %s; added with its own %s; its QR reads %r; the team's QR still shown"
          % (closed, nopw, added, got3))
    pg.click("#sheet .sh-close")
    # 4c the member's phone, scanning W7OWN's QR (a name and the lead's key): "Join as W7OWN", their own password
    pg.evaluate("S.cfg.enrolled = {}; roleFields(); $('#main').hidden = true; $('#connect').hidden = false")
    pg.evaluate("l => rtDeliver(JSON.stringify({ev: 'enrollScan', line: l, url: 'http://10.253.1.1:21801/enroll', app: 'openeoc', user: 'W7OWN', team: false, key: 'dW9LoJ0JmymSPLael9M7kg'}))", w3)
    who, say = pg.inner_text("#enrollWho"), pg.inner_text("#cstatus")
    check(who.startswith("Join as W7OWN") and "own password" in say and not pg.is_visible("#enrollCall"), "4c a member's own QR scanned: %r, %r" % (who, say))
    pg.evaluate("$('#enrollBox').hidden = true; $('#main').hidden = false; $('#connect').hidden = true")
    # 6 [TEAM_JOIN_V1] the team's QR scanned: a call sign and the team password; Join enrolls the call sign
    # a joining phone: its first screen ([JOINED_IS_DONE_V1] a phone that has joined is never offered the scan: not joined here)
    pg.evaluate("__sent.length = 0; S.cfg.enrolled = {}; roleFields(); $('#main').hidden = true; $('#connect').hidden = false")
    pg.evaluate("l => rtDeliver(JSON.stringify({ev: 'enrollScan', line: l, url: 'http://192.168.43.1:21801/enroll', app: 'openeoc', user: '*', team: true, key: 'dW9LoJ0JmymSPLael9M7kg'}))", w1)
    asks = pg.is_visible("#enrollCall") and pg.is_visible("#enrollPw")
    pg.fill("#enrollCall", "w7new"); pg.fill("#enrollPw", "echo-2026"); pg.click("#enrollGo")
    sent6 = [x for x in pg.evaluate("__sent") if x[0] == "enroll"]
    check(asks and sent6 == [["enroll", {"line": "fdfenroll1 http://192.168.43.1:21801/enroll openeoc W7NEW dW9LoJ0JmymSPLael9M7kg", "password": "echo-2026"}]],
          "6 the team's QR: asks a call sign and the password (%s); Join enrolls %s" % (asks, sent6))
    # 6b [CONNECT_QR_FIRST_V1] Your name typed before (nameGiven): the team's QR asks the team password only
    pg.evaluate("__sent.length = 0; S.cfg.nameGiven = true; $('#connect').name.value = 'kj7abc'; $('#enrollBox').hidden = true; $('#enrollGo').disabled = false")
    pg.evaluate("l => rtDeliver(JSON.stringify({ev: 'enrollScan', line: l, url: 'http://192.168.43.1:21801/enroll', app: 'openeoc', user: '*', team: true, key: 'dW9LoJ0JmymSPLael9M7kg'}))", w1)
    only = not pg.is_visible("#enrollCall") and pg.is_visible("#enrollPw") and "KJ7ABC" in pg.inner_text("#cstatus")
    pg.fill("#enrollPw", "echo-2026"); pg.click("#enrollGo")
    sent6b = [x for x in pg.evaluate("__sent") if x[0] == "enroll"]
    check(only and sent6b == [["enroll", {"line": "fdfenroll1 http://192.168.43.1:21801/enroll openeoc KJ7ABC dW9LoJ0JmymSPLael9M7kg", "password": "echo-2026"}]],
          "6b Your name given: the team password only (%s, %r); Join enrolls %s" % (only, pg.inner_text("#cstatus"), sent6b))
    # 5 a refused enrollment (a fresh page)
    pg.goto("file://" + os.path.join(APP, "ui", "index.html"))
    pg.evaluate("rtInit({dev: 'p2', name: 'P', door: '', role: 'team', team: 'Alpha', platform: 'android', lock: '', build: 'test', invited: '', lan: '', enrolled: {}})")
    pg.wait_for_selector("#pick:not([hidden])", timeout=5000); pg.click("[data-pick=team]")   # [RT_APP_PICKER_V1]
    pg.wait_for_selector("#connect:not([hidden])", timeout=5000)
    pg.evaluate("l => rtDeliver(JSON.stringify({ev: 'enrollScan', line: l, url: 'http://192.168.43.1:21801/enroll', app: 'openeoc', user: 'ann'}))", "fdfenroll1 http://192.168.43.1:21801/enroll openeoc ann")
    pg.fill("#enrollPw", "wrong"); pg.click("#enrollGo")
    pg.evaluate("rtDeliver(JSON.stringify({ev: 'enrollFailed', text: \"that user and password are not a member of echo's team\"}))")
    check("not a member of echo" in pg.inner_text("#cstatus") and not pg.is_disabled("#enrollGo") and pg.is_visible("#enrollBox"),
          "5 a refused enrollment is said (%s), and Join can be pressed again" % pg.inner_text("#cstatus"))
    # 7 [CHILDREN_JOIN_V1] Join EOC: a new team, named here, by the EOC's teams password
    pg.evaluate("__sent.length = 0; $('#enrollBox').hidden = true")
    pg.click("#manual > summary")                                          # [CONNECT_QR_FIRST_V1] Start a team is under Manual login
    pg.fill("[name=door]", "")
    pg.click("#joinEoc"); noNode = "the Node first" in pg.inner_text("#cstatus")
    pg.fill("[name=door]", "10.170.170.1"); pg.click("#joinEoc")
    asks7 = pg.is_visible("#enrollCall") and "team's name" in pg.inner_text("#enrollCallL") and "Join the EOC" in pg.inner_text("#enrollWho")
    pg.fill("#enrollCall", "Red Cross"); pg.fill("#enrollPw", "teams-2026"); pg.click("#enrollGo")
    sent7 = [x for x in pg.evaluate("__sent") if x[0] == "enroll"]
    pg.evaluate("rtDeliver(JSON.stringify({ev: 'enrolled', user: 'Red Cross', role: 'Red Cross', lead: true, child: true, door: '10.170.170.1', invited: 'openeoc-red_cross in frognet'}))")
    conn7 = [x[1] for x in pg.evaluate("__sent") if x[0] == "send" and x[1].get("op") == "connect"]
    c7 = conn7[-1] if conn7 else {}
    check(noNode and asks7 and sent7 == [["enroll", {"line": "fdfenroll1 http://10.170.170.1/dc/ram.php openeoc Red%20Cross", "password": "teams-2026"}]]
          and c7.get("role") == "team" and c7.get("user") == "Red Cross" and c7.get("password") == "teams-2026",
          "7 Start a team: Node asked first %s; the team's name and the teams password %s; Join enrolls %s; connects %s" % (noNode, asks7, sent7, {k: c7.get(k) for k in ("door", "role", "team", "user")}))
    b.close()
print("%d failed" % len(fails) if fails else "ALL PASS"); sys.exit(1 if fails else 0)
