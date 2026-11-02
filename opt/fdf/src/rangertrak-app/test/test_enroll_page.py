#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
# test_enroll_page.py -- [ENROLL_V1] the phone's page (ui/), in Chromium, with the Android side stood in for by a
# recording rtNative (the native side's events are delivered as MainActivity delivers them):
#   1 Scan to join is on the first screen of an Android phone
#   2 a scanned code (enrollScan) asks for that user's password; Join hands the line and the password to the native side
#   3 enrolled (lead of echo, the door): the page connects as that user, Team view, team Echo, the door, the password
#   4 the lead's Team members button; the members sheet (members): adding a member hands its name and password to the
#     native side; a member's QR, read by zxing (the phone scanner's library), is that member's enrollment line at the
#     address chosen, and choosing another address changes it
#   5 a refused enrollment is said, and Join can be pressed again
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zxing_read  # noqa: E402
from playwright.sync_api import sync_playwright

APP = zxing_read.APP
fails = []
def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what); (None if ok else fails.append(what))
zxing_read.ready()

STUB = """window.__sent = []; window.rtNative = {
  send: l => __sent.push(['send', JSON.parse(l)]), scanTeam: () => __sent.push(['scan']),
  enroll: j => __sent.push(['enroll', JSON.parse(j)]), teamMembers: () => __sent.push(['members']),
  addMember: (n, p) => __sent.push(['add', n, p]), removeMember: n => __sent.push(['remove', n]),
  teamHost: () => __sent.push(['host']) }"""
with tempfile.TemporaryDirectory() as tmp, sync_playwright() as pw:
    b = pw.chromium.launch(); pg = b.new_page(viewport={"width": 420, "height": 860})
    pg.add_init_script(STUB)
    pg.goto("file://" + os.path.join(APP, "ui", "index.html"))
    pg.evaluate("rtInit({dev: 'p1', name: 'Pixel', door: '', role: 'team', team: 'Alpha', platform: 'android', lock: '', build: 'test', invited: '', lan: '', enrolled: {}})")
    pg.wait_for_selector("#connect:not([hidden])", timeout=5000)
    check(pg.is_visible("#scanTeam") and pg.inner_text("#scanTeam") == "Scan to join", "1 Scan to join is on the first screen")
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
    btn = pg.is_visible("#membersBtn")
    pg.click("#membersBtn")
    addrs = [{"label": "this phone's hotspot", "addr": "192.168.43.1"}, {"label": "FrogNet (the Internet)", "addr": "10.253.1.1"}]
    pg.evaluate("a => rtDeliver(JSON.stringify({ev: 'members', app: 'rangertrak', team: 'echo', members: ['ann'], addrs: a, port: 21801, sponsoring: true}))", addrs)
    pg.fill("#memName", "KJ7ABC"); pg.fill("#memPw", "frog"); pg.click("#memAdd")
    added = ["add", "KJ7ABC", "frog"] in pg.evaluate("__sent")
    pg.evaluate("a => rtDeliver(JSON.stringify({ev: 'members', app: 'rangertrak', team: 'echo', members: ['ann', 'KJ7ABC'], addrs: a, port: 21801, sponsoring: true}))", addrs)
    pg.click("[data-memqr=KJ7ABC]"); pg.wait_for_selector("#memQr svg", timeout=3000)
    q1 = os.path.join(tmp, "q1.png"); pg.locator("#memQr svg").screenshot(path=q1)
    pg.select_option("#memAt", "10.253.1.1"); pg.wait_for_function("document.querySelector('#memQr .who').textContent.includes('10.253.1.1')", timeout=3000)
    q2 = os.path.join(tmp, "q2.png"); pg.locator("#memQr svg").screenshot(path=q2)
    got = zxing_read.read([q1, q2])
    w1 = "fdfenroll1 http://192.168.43.1:21801/enroll rangertrak KJ7ABC"; w2 = "fdfenroll1 http://10.253.1.1:21801/enroll rangertrak KJ7ABC"
    check(btn and ["members"] in pg.evaluate("__sent") and added and got.get(q1) == w1 and got.get(q2) == w2,
          "4 the lead's Team members: button %s, add handed over %s; KJ7ABC's QR reads %r, then %r" % (btn, added, got.get(q1), got.get(q2)))
    pg.click("#sheet .sh-close")
    # 5 a refused enrollment (a fresh page)
    pg.goto("file://" + os.path.join(APP, "ui", "index.html"))
    pg.evaluate("rtInit({dev: 'p2', name: 'P', door: '', role: 'team', team: 'Alpha', platform: 'android', lock: '', build: 'test', invited: '', lan: '', enrolled: {}})")
    pg.wait_for_selector("#connect:not([hidden])", timeout=5000)
    pg.evaluate("l => rtDeliver(JSON.stringify({ev: 'enrollScan', line: l, url: 'http://192.168.43.1:21801/enroll', app: 'rangertrak', user: 'ann'}))", "fdfenroll1 http://192.168.43.1:21801/enroll rangertrak ann")
    pg.fill("#enrollPw", "wrong"); pg.click("#enrollGo")
    pg.evaluate("rtDeliver(JSON.stringify({ev: 'enrollFailed', text: \"that user and password are not a member of echo's team\"}))")
    check("not a member of echo" in pg.inner_text("#cstatus") and not pg.is_disabled("#enrollGo") and pg.is_visible("#enrollBox"),
          "5 a refused enrollment is said (%s), and Join can be pressed again" % pg.inner_text("#cstatus"))
    b.close()
print("%d failed" % len(fails) if fails else "ALL PASS"); sys.exit(1 if fails else 0)
