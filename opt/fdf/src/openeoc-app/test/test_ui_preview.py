#!/usr/bin/env python3
# Copyright (C) 2016-2026 Fawcett Innovations LLC. SPDX-License-Identifier: GPL-2.0-only
"""test_ui_preview.py -- [UI_PREVIEW_V1] the UI preview run end to end in a real browser, from file:// as a reviewer
opens it: EOC, two Field leads (Team, Hospital), a member, EOCAdmin, each its own tab, one browser.
  * the demo loads; alpha claims as a Team and Teams appears at the EOC; a second claim of alpha is refused
  * the lead sets the team password; a member joins with it
  * the hospital claims, reports Divert: Health and Medical turns yellow at the EOC
  * the EOC assigns Team Alpha; the lead sees it and completes it
  * the hospital requests; the EOC approves it; the hospital sees Approved
  * EOCAdmin's ICS 209 is filled from the records; kept final; the IAP renders
  * no page error anywhere; every screen of every app renders at phone width with no horizontal scroll"""
import os, sys
from playwright.sync_api import sync_playwright

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui-preview")
U = lambda p: "file://" + os.path.abspath(os.path.join(ROOT, p))
errs, ok = [], True


def check(c, what):
    global ok
    print(("PASS " if c else "FAIL ") + what)
    ok = ok and bool(c)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    ctx = b.new_context(viewport={"width": 1280, "height": 860})
    def page(p):
        x = ctx.new_page(); x.on("pageerror", lambda e: errs.append(p + ": " + str(e))); x.goto(U(p)); return x
    eoc = page("eoc/index.html")
    eoc.click("text=Load the demo: authorized, no one joined")
    eoc.wait_for_selector("text=Community lifelines")
    check(eoc.locator("text=no organization has joined yet").count() == 1, "demo loaded: lifelines shown, no one on the network")

    t = page("field/index.html")
    t.click("#apps >> text=Team")
    t.select_option("#cl", "alpha"); t.fill("#cname", "Team Alpha"); t.fill("#clead", "K7ABC"); t.fill("#cpw", "alpha1"); t.click("#claim")
    t.wait_for_selector("h2:has-text('Team Alpha')")
    eoc.wait_for_selector("text=Team Alpha")
    check(eoc.locator(".card .t:has-text('Teams')").count() == 1, "alpha claimed as a Team: the Teams category appears at the EOC")

    t2 = page("field/index.html")
    t2.click("#apps >> text=Team")
    t2.select_option("#cl", "alpha"); t2.fill("#clead", "K7ZZZ"); t2.fill("#cpw", "alpha1"); t2.click("#claim")
    check("no replacement" in t2.inner_text("#status"), "a second device's claim of alpha is refused: " + t2.inner_text("#status"))

    t.click("#nav >> text=Members"); t.fill("#tpw", "teampw"); t.click("#tpwSet")
    t.wait_for_selector("text=Change")
    t2.reload(); t2.select_option("#jl", "alpha"); t2.fill("#jcall", "k7def"); t2.fill("#jpw", "teampw"); t2.click("#mjoin")
    t2.wait_for_selector("text=member K7DEF")
    t.wait_for_selector("td:has-text('K7DEF')")
    check(True, "a member joined with the team password; the lead's Members lists K7DEF")

    h = page("field/index.html")
    h.click("#apps >> text=Hospital")
    h.select_option("#cl", "cascade-general"); h.fill("#cname", "Cascade General"); h.fill("#clead", "RN Kim"); h.fill("#cpw", "cgh1"); h.click("#claim")
    h.wait_for_selector("text=ED beds available")
    h.select_option("#stf select[name=state]", "Divert"); h.fill("#stf input[name=edBeds]", "2"); h.click("#stSend")
    eoc.wait_for_selector("[data-ll=health] .dot.yellow")
    check(eoc.locator(".card .t:has-text('Hospitals')").count() == 1, "the hospital joined as Hospitals and reported Divert: Health and Medical is yellow")

    eoc.click("#nav >> text=Assignments"); eoc.click("#na")
    eoc.select_option("#dlg select[name=to]", "alpha"); eoc.fill("#dlg input[name=what]", "Welfare checks, Maple St"); eoc.click("#dlg .df button:text-is('Assign')")
    t.click("#nav >> text=Overview"); t.wait_for_selector("td:has-text('Welfare checks, Maple St')")
    t.click("[data-st=Complete]")
    eoc.wait_for_selector("td .pill:has-text('Complete')")
    check(True, "the EOC assigned Team Alpha; the lead saw it and completed it")

    h.click("#nav >> text=Requests"); h.select_option("#rf select[name=resource]", "Generator, 100 kW"); h.click("#rsend")
    eoc.click("#nav >> text=Requests"); eoc.wait_for_selector("td:has-text('Generator, 100 kW')")
    eoc.click("[data-s=Approved]"); eoc.click("#dlg .df >> text=Approved")
    h.wait_for_selector("td .pill:has-text('Approved')")
    check(True, "the hospital's request: approved at the EOC, Approved at the hospital")

    a = page("eocadmin/index.html")
    a.click(".card:has-text('ICS 209')")
    v = a.input_value("#ff textarea[name=orgs]")
    check("Cascade General" in v and "Divert" in v and "Team Alpha" in v, "EOCAdmin's ICS 209 filled from the records")
    a.click("#final"); a.click("#dlg .df >> text=Make final")
    a.wait_for_selector("text=Final: read only")
    a.click("#nav >> text=IAP"); a.wait_for_selector("text=ICS 206")
    check("Cascade General" in a.inner_text("#view"), "the IAP renders, its medical plan with the hospital")

    # every screen of every app, at phone width
    bad = []
    for p, tab in (("eoc/index.html", eoc), ("field/index.html", t), ("eocadmin/index.html", a)):
        tab.set_viewport_size({"width": 390, "height": 800})
        for i in range(tab.locator("#nav button").count()):
            tab.locator("#nav button").nth(i).click()
            w = tab.evaluate("document.documentElement.scrollWidth")
            if w > 391:
                bad.append("%s #%d %dpx" % (p, i, w))
    check(not bad, "every screen at 390 px with no horizontal scroll %s" % bad)
    check(not errs, "no page errors %s" % errs)
    b.close()
print("RESULT", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
