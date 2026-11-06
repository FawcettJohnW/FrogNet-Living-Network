#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
# test_enroll_page.py -- [ENROLL_V1] the phone's page (ui/), in Chromium, with the Android side stood in for by a
# recording rtNative (the native side's events are delivered as MainActivity delivers them):
#   1 Scan to join is on the first screen of an Android phone
#   6 [TEAM_JOIN_V1] the team's QR scanned asks a call sign and the team password; Join enrolls the call sign
#   2 a scanned code (enrollScan) asks for that user's password; Join hands the line and the password to the native side
#   3 enrolled (lead of echo, the door): the page connects as that user, Team view, team Echo, the door, the password
#   4 the lead's Team members button; the members sheet (members): adding a member hands its name and password to the
#     native side; a member's QR, read by zxing (the phone scanner's library), is that member's enrollment line at the
#     address chosen, and choosing another address changes it
#   5 a refused enrollment is said, and Join can be pressed again
#   7 [CHILDREN_JOIN_V1] Join EOC on the first screen: the team's name and the EOC's teams password; Join enrolls the
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
    check(pg.is_visible("#scanTeam") and pg.inner_text("#scanTeam").startswith("Scan to join") and pg.is_visible("#joinEoc"), "1 Scan to join and Join EOC are on the first screen")
    pg.click("#scanTeam")
    line = "fdfenroll1 http://10.170.170.1/dc/ram.php rangertrak kim"
    pg.evaluate("l => rtDeliver(JSON.stringify({ev: 'enrollScan', line: l, url: 'http://10.170.170.1/dc/ram.php', app: 'rangertrak', user: 'kim'}))", line)
    shown = pg.is_visible("#enrollBox") and "kim" in pg.inner_text("#enrollWho")
    pg.fill("#enrollPw", "kim-2026"); pg.click("#enrollGo")
    sent = pg.evaluate("__sent")
    check(shown and ["scan"] in sent and ["enroll", {"line": line, "password": "kim-2026"}] in sent,
          "2 the scanned code asks for kim's password; Join hands over the line and the password: %s" % [s for s in sent if s[0] == "enroll"])
    pg.evaluate("rtDeliver(JSON.stringify({ev: 'enrolled', user: 'kim', role: 'echo', lead: true, door: '10.170.170.1', invited: 'rangertrak-kim in frognet'}))")
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
    pg.evaluate("a => rtDeliver(JSON.stringify({ev: 'members', app: 'rangertrak', team: 'echo', members: [], addrs: a, port: 21801, sponsoring: true, teamPassword: false}))", addrs)
    noQr = pg.locator("#memQr svg").count() == 0 and "set the team password" in pg.inner_text("#memQr")
    pg.fill("#teamPw", "echo-2026"); pg.click("#teamPwSet")
    setPw = ["teamPw", "echo-2026"] in pg.evaluate("__sent")
    pg.evaluate("a => rtDeliver(JSON.stringify({ev: 'members', app: 'rangertrak', team: 'echo', members: ['KJ7ABC'], addrs: a, port: 21801, sponsoring: true, teamPassword: true}))", addrs)
    pg.wait_for_selector("#memQr svg", timeout=3000)
    q1 = os.path.join(tmp, "q1.png"); pg.locator("#memQr svg").screenshot(path=q1)
    pg.select_option("#memAt", "10.253.1.1"); pg.wait_for_function("document.querySelector('#memQr .who').textContent.includes('10.253.1.1')", timeout=3000)
    q2 = os.path.join(tmp, "q2.png"); pg.locator("#memQr svg").screenshot(path=q2)
    got = zxing_read.read([q1, q2])
    w1 = "fdfenroll1 http://192.168.43.1:21801/enroll rangertrak *"; w2 = "fdfenroll1 http://10.253.1.1:21801/enroll rangertrak *"
    joined = "KJ7ABC" in pg.inner_text("#sheet")
    pg.evaluate("rtDeliver(JSON.stringify({ev: 'world', who: {d9: {name: 'Drone 9'}, x1: {name: 'Other'}}, pos: {d9: {kind: 'drone', unit: 'echo', name: 'Drone 9'}, x1: {kind: 'team', team: 'Alpha', name: 'Other'}}}))")
    nowEcho = pg.inner_text("#memNow"); joined = joined and "Drone 9" in nowEcho and "drone" in nowEcho and "Other" not in nowEcho
    check(btn and ["members"] in pg.evaluate("__sent") and noQr and setPw and got.get(q1) == w1 and got.get(q2) == w2 and joined,
          "4 the lead's team: no QR before the password %s; the password handed over %s; the team's QR reads %r, then %r; KJ7ABC listed, on now Drone 9 (drone), not Alpha's %s" % (noQr, setPw, got.get(q1), got.get(q2), joined))
    pg.click("#sheet .sh-close")
    # 6 [TEAM_JOIN_V1] the team's QR scanned: a call sign and the team password; Join enrolls the call sign
    pg.evaluate("__sent.length = 0; $('#main').hidden = true; $('#connect').hidden = false")   # a joining phone: its first screen
    pg.evaluate("l => rtDeliver(JSON.stringify({ev: 'enrollScan', line: l, url: 'http://192.168.43.1:21801/enroll', app: 'rangertrak', user: '*', team: true}))", w1)
    asks = pg.is_visible("#enrollCall") and pg.is_visible("#enrollPw")
    pg.fill("#enrollCall", "w7new"); pg.fill("#enrollPw", "echo-2026"); pg.click("#enrollGo")
    sent6 = [x for x in pg.evaluate("__sent") if x[0] == "enroll"]
    check(asks and sent6 == [["enroll", {"line": "fdfenroll1 http://192.168.43.1:21801/enroll rangertrak W7NEW", "password": "echo-2026"}]],
          "6 the team's QR: asks a call sign and the password (%s); Join enrolls %s" % (asks, sent6))
    # 5 a refused enrollment (a fresh page)
    pg.goto("file://" + os.path.join(APP, "ui", "index.html"))
    pg.evaluate("rtInit({dev: 'p2', name: 'P', door: '', role: 'team', team: 'Alpha', platform: 'android', lock: '', build: 'test', invited: '', lan: '', enrolled: {}})")
    pg.wait_for_selector("#pick:not([hidden])", timeout=5000); pg.click("[data-pick=team]")   # [RT_APP_PICKER_V1]
    pg.wait_for_selector("#connect:not([hidden])", timeout=5000)
    pg.evaluate("l => rtDeliver(JSON.stringify({ev: 'enrollScan', line: l, url: 'http://192.168.43.1:21801/enroll', app: 'rangertrak', user: 'ann'}))", "fdfenroll1 http://192.168.43.1:21801/enroll rangertrak ann")
    pg.fill("#enrollPw", "wrong"); pg.click("#enrollGo")
    pg.evaluate("rtDeliver(JSON.stringify({ev: 'enrollFailed', text: \"that user and password are not a member of echo's team\"}))")
    check("not a member of echo" in pg.inner_text("#cstatus") and not pg.is_disabled("#enrollGo") and pg.is_visible("#enrollBox"),
          "5 a refused enrollment is said (%s), and Join can be pressed again" % pg.inner_text("#cstatus"))
    # 7 [CHILDREN_JOIN_V1] Join EOC: a new team, named here, by the EOC's teams password
    pg.evaluate("__sent.length = 0; $('#enrollBox').hidden = true")
    pg.fill("[name=door]", "")
    pg.click("#joinEoc"); noNode = "the Node first" in pg.inner_text("#cstatus")
    pg.fill("[name=door]", "10.170.170.1"); pg.click("#joinEoc")
    asks7 = pg.is_visible("#enrollCall") and "team's name" in pg.inner_text("#enrollCallL") and "Join the EOC" in pg.inner_text("#enrollWho")
    pg.fill("#enrollCall", "Red Cross"); pg.fill("#enrollPw", "teams-2026"); pg.click("#enrollGo")
    sent7 = [x for x in pg.evaluate("__sent") if x[0] == "enroll"]
    pg.evaluate("rtDeliver(JSON.stringify({ev: 'enrolled', user: 'Red Cross', role: 'Red Cross', lead: true, child: true, door: '10.170.170.1', invited: 'rangertrak-red_cross in frognet'}))")
    conn7 = [x[1] for x in pg.evaluate("__sent") if x[0] == "send" and x[1].get("op") == "connect"]
    c7 = conn7[-1] if conn7 else {}
    check(noNode and asks7 and sent7 == [["enroll", {"line": "fdfenroll1 http://10.170.170.1/dc/ram.php rangertrak Red%20Cross", "password": "teams-2026"}]]
          and c7.get("role") == "team" and c7.get("user") == "Red Cross" and c7.get("password") == "teams-2026",
          "7 Join EOC: Node asked first %s; the team's name and the teams password %s; Join enrolls %s; connects %s" % (noNode, asks7, sent7, {k: c7.get(k) for k in ("door", "role", "team", "user")}))
    b.close()
print("%d failed" % len(fails) if fails else "ALL PASS"); sys.exit(1 if fails else 0)
